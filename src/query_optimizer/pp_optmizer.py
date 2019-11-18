"""Finds Optimal Probabilistic Predicates for an expression"""
import src.constants as constants
from src.catalog.catalog import Catalog
from src.expression.abstract_expression import AbstractExpression, ExpressionType
from src.expression.comparison_expression import ComparisonExpression
from src.expression.constant_value_expression import ConstantValueExpression
from src.expression.logical_expression import LogicalExpression
from src.query_optimizer.memo import Memo


class PPOptmizer:

    def __init__(self, catalog: Catalog, predicates: list, accuracy_budget: float = 0.9):
        self._catalog = catalog
        self._memo = Memo()
        self._pp_handler = self._catalog.getProbPredicateHandler()
        self._predicates = predicates
        self.accuracy_budget = accuracy_budget

    def _get_pp_stats(self) -> dict:
        """Query PP Stats from Catalog

        :return:
        dict, A dictionary containing cost information of all pp filters
        """
        filter_names = self._pp_handler.listProbilisticFilters()
        filter_names = [f[0] for f in filter_names]
        stats = {}
        for f in filter_names:
            rows = self._pp_handler.getProbabilisticFilter(f)
            filter_dict = {}
            for row in rows:
                filter_dict[row[7]] = {'R': row[2], 'C': row[3], 'A': row[4]}
            stats[f] = filter_dict
        return stats

    def _find_model(self, pp_name, pp_stats, accuracy_budget=0.9):
        possible_models = pp_stats[pp_name]
        best = []

        for possible_model in possible_models:
            if possible_models[possible_model]["A"] < accuracy_budget:
                continue
            if best == []:
                best = [possible_model, self._compute_cost_red_rate(
                    possible_models[possible_model]["C"],
                    possible_models[possible_model]["R"]),
                        possible_models[possible_model]["R"]]
            else:
                alternative_best_cost = self._compute_cost_red_rate(
                    possible_models[possible_model]["C"],
                    possible_models[possible_model]["R"])
                if alternative_best_cost < best[1]:
                    best = [possible_model, alternative_best_cost,
                            possible_models[possible_model]["R"]]

        if best == []:
            return None, 0
        else:
            return best[0], best[2]

    def _wrangler(self, expression: LogicalExpression, label_desc: dict) -> list:
        """ Generate possible enumerations of the `expression`

        TODO: Currently supporting LogicalExpression, but its a easy modification to support AbstractExpression

        :param expression: A Logical Expression
        :param label_desc: Description of all possible PP filters of a type
        :return:
        list, A list of Logical Expressions which are possible enumerations of the input expression
        """
        leftExpr = expression.getLeftExpression()
        rightExpr = expression.getRightExpression()
        operator = expression.getOperator()  # AND / OR

        new_left_exprs = [leftExpr]
        new_right_exprs = [rightExpr]

        # Explore left expr
        if leftExpr.etype == ExpressionType.COMPARE_EQUAL:
            lValue = leftExpr.left.evaluate()
            rValue = leftExpr.right.evaluate()
            rValues = label_desc[lValue][1]  # fetching all possible rvalues
            new_not_expr = None
            for rVal in rValues:
                if rVal != rValue:
                    new_r_expr = ConstantValueExpression(rVal)
                    new_comp_expr = ComparisonExpression(ExpressionType.COMPARE_NOT_EQUAL, leftExpr.left, new_r_expr)
                    if not new_not_expr:
                        new_not_expr = new_comp_expr
                    else:
                        new_not_expr = LogicalExpression(new_not_expr, 'AND', new_comp_expr)
            new_left_exprs.append(new_not_expr)
        elif leftExpr.etype == ExpressionType.COMPARE_LOGICAL:
            new_left_exprs.extend(self._wrangler(leftExpr, label_desc))

        # Explore right expr
        if rightExpr.etype == ExpressionType.COMPARE_EQUAL:
            lValue = rightExpr.left.evaluate()
            rValue = rightExpr.right.evaluate()
            rValues = label_desc[lValue][1]  # fetching all possible rvalues
            new_not_expr = None
            for rVal in rValues:
                if rVal != rValue:
                    new_r_expr = ConstantValueExpression(rVal)
                    new_comp_expr = ComparisonExpression(ExpressionType.COMPARE_NOT_EQUAL, rightExpr.left, new_r_expr)
                    if not new_not_expr:
                        new_not_expr = new_comp_expr
                    else:
                        new_not_expr = LogicalExpression(new_not_expr, 'AND', new_comp_expr)
            new_right_exprs.append(new_not_expr)
        elif rightExpr.etype == ExpressionType.COMPARE_LOGICAL:
            new_right_exprs.extend(self._wrangler(leftExpr, label_desc))

        enumerated_exprs = []
        for left in new_left_exprs:
            for right in new_right_exprs:
                new_logical_expr = LogicalExpression(left, operator, right)
                enumerated_exprs.append(new_logical_expr)
        return enumerated_exprs

    def _compute_expression_costs(self, expression: AbstractExpression, stats) -> float:
        """Compute cost of applying PP filters to an expression

        :param expression: Input Expression (Logical Expression)
        :return: Execution Cost of the  expressions
        """
        if isinstance(expression, LogicalExpression):
            left_expression = expression.getLeftExpression()
            right_expression = expression.getRightExpression()

            if isinstance(left_expression, LogicalExpression):
                left_cost = self._compute_cost_red_rate(left_expression, stats)
            else:
                left_cost = 0
            if isinstance(right_expression, LogicalExpression):
                right_cost = self._compute_cost_red_rate(right_expression, stats)
            else:
                right_cost = 0

            filter = str(expression)
            if left_cost != 0 and right_cost != 0:
                if expression.operator == "AND":
                    reduction_rate = left_cost + right_cost - left_cost * right_cost
                elif expression.operator == "OR":
                    reduction_rate = left_cost * right_cost
                else:
                    raise ValueError("Invalid operator: " + expression.operator)
            else:
                _, reduction_rate = self._find_model(filter, stats)
            return reduction_rate

    def _compute_cost_red_rate(self, cost: float, reduction_rate: float) -> float:
        """Compute Execution Cost of PP filter"""
        assert (0 <= reduction_rate <= 1)
        if reduction_rate == 0:
            reduction_rate = 0.000001
        return cost / reduction_rate

    def execute(self):
        """Finds optimal order expression based on PP execution cost evaluation."""

        stats = self._get_pp_stats()
        filter_names = stats.keys()

        # TODO: Need to query this from DB. Check how to do this. Or we cal split the filter names to generate this.
        label_desc = {"t": [constants.DISCRETE, ["sedan", "suv", "truck", "van"]],
                      "s": [constants.CONTINUOUS, [40, 50, 60, 65, 70]],
                      "c": [constants.DISCRETE,
                            ["white", "red", "black", "silver"]],
                      "i": [constants.DISCRETE,
                            ["pt335", "pt342", "pt211", "pt208"]],
                      "o": [constants.DISCRETE,
                            ["pt335", "pt342", "pt211", "pt208"]]}

        # TODO: Generalize this all list of expression. A for loop.
        expr = self._predicates[0]
        enumerations = self._wrangler(expr, label_desc)
        candidate_cost_list = []
        for candidate in enumerations:
            execution_cost = self._compute_expression_costs(candidate, stats)
            candidate_cost_list.append((candidate, execution_cost))

        def sort_on_second_index(x):
            return x[1]

        zipped_cost_list = list(zip(enumerations, candidate_cost_list))
        zipped_cost_list.sort(key=sort_on_second_index)

        return zipped_cost_list[0]
        # Sort based on optimal execution cost
        # return the best candidate


if __name__ == '__main__':
    catalog = Catalog(constants.UADETRAC)
    pp_handler = catalog.getProbPredicateHandler()
    pp_filter_names = pp_handler.listProbilisticFilters()
    pp_filter_names = [f[0] for f in pp_filter_names]
    print(pp_filter_names)